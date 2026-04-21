"""Unit and e2e tests for temporal edge indexing."""
import json
import os
import time
import uuid
import pytest
from unittest.mock import MagicMock

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class TestTemporalEdgeUnit:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        iris_mock = MagicMock()
        e._iris_obj = lambda: iris_mock
        return e, iris_mock

    def test_create_edge_temporal_calls_classmethod(self):
        engine, mock = self._make_engine()
        engine.create_edge_temporal("A", "REL", "B", 1712000000)
        assert mock.classMethodVoid.called

    def test_bulk_create_calls_bulk_insert(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = "3"
        result = engine.bulk_create_edges_temporal([
            {"s": "A", "p": "REL", "o": "B", "ts": 1712000000},
            {"s": "A", "p": "REL", "o": "C", "ts": 1712000001},
            {"s": "A", "p": "REL", "o": "D", "ts": 1712000002},
        ])
        assert result == 3

    def test_timestamp_none_sends_empty_string(self):
        engine, mock = self._make_engine()
        engine.create_edge_temporal("A", "REL", "B", timestamp=None)
        call_args = mock.classMethodVoid.call_args[0]
        assert "" in call_args

    def test_get_edges_in_window_returns_list(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '[{"s":"A","p":"REL","o":"B","ts":100,"w":1}]'
        result = engine.get_edges_in_window("A", "REL", 0, 200)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_get_edges_in_window_empty_returns_empty_list(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '[]'
        result = engine.get_edges_in_window("A", "REL", 0, 200)
        assert result == []

    def test_get_edge_velocity_returns_int(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = "42"
        result = engine.get_edge_velocity("A", 300)
        assert result == 42

    def test_find_burst_nodes_returns_list(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '[{"id":"A","velocity":100}]'
        result = engine.find_burst_nodes("REL", 60, 50)
        assert len(result) == 1
        assert result[0]["id"] == "A"

    def test_find_burst_nodes_empty(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '[]'
        result = engine.find_burst_nodes("REL", 60, 9999)
        assert result == []


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestTemporalEdgeE2E:

    PREFIX = f"TE_{uuid.uuid4().hex[:6]}"

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        yield
        try:
            self.engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
        except:
            pass

    def test_bulk_insert_and_count(self):
        now = int(time.time())
        edges = [{"s": f"{self.PREFIX}:A", "p": "SENDS", "o": f"{self.PREFIX}:B{i}", "ts": now + i} for i in range(100)]
        count = self.engine.bulk_create_edges_temporal(edges)
        assert count == 100

    def test_window_query_returns_correct_edges(self):
        now = int(time.time())
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:W", "p": "LINK", "o": f"{self.PREFIX}:X", "ts": now - 100},
            {"s": f"{self.PREFIX}:W", "p": "LINK", "o": f"{self.PREFIX}:Y", "ts": now - 50},
            {"s": f"{self.PREFIX}:W", "p": "LINK", "o": f"{self.PREFIX}:Z", "ts": now + 1000},
        ])
        edges = self.engine.get_edges_in_window(f"{self.PREFIX}:W", "LINK", now - 200, now)
        targets = {e["o"] for e in edges}
        assert f"{self.PREFIX}:X" in targets
        assert f"{self.PREFIX}:Y" in targets
        assert f"{self.PREFIX}:Z" not in targets

    def test_velocity_counts_bucket(self):
        now = int(time.time())
        edges = [{"s": f"{self.PREFIX}:V", "p": "HIT", "o": f"{self.PREFIX}:T{i}", "ts": now - i} for i in range(50)]
        self.engine.bulk_create_edges_temporal(edges)
        velocity = self.engine.get_edge_velocity(f"{self.PREFIX}:V", 300)
        assert velocity >= 50

    def test_burst_detection(self):
        now = int(time.time())
        edges = [{"s": f"{self.PREFIX}:BURST", "p": "SENDS", "o": f"{self.PREFIX}:D{i}", "ts": now - i} for i in range(100)]
        self.engine.bulk_create_edges_temporal(edges)
        bursts = self.engine.find_burst_nodes("SENDS", 300, 50)
        burst_ids = {b["id"] for b in bursts}
        assert f"{self.PREFIX}:BURST" in burst_ids

    def test_backward_compat_kg_out_populated(self):
        now = int(time.time())
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:BC", "p": "COMPAT", "o": f"{self.PREFIX}:BC2", "ts": now},
        ])
        try:
            import iris
            try:
                irispy = iris.createIRIS(self.conn)
            except TypeError:
                import intersystems_iris
                irispy = intersystems_iris.createIRIS(self.conn)
            val = irispy.get("^KG", "out", 0, f"{self.PREFIX}:BC", "COMPAT", f"{self.PREFIX}:BC2")
            assert val is not None
        except ImportError:
            pytest.skip("iris.createIRIS not available on this Python version")


class TestTemporalPreAggUnit:
    """Unit tests for pre-aggregation Python wrappers (mock IRIS, no container).
    Tests written FIRST per TDD — will fail until engine.py wrappers are added.
    """

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        iris_mock = MagicMock()
        e._iris_obj = lambda: iris_mock
        return e, iris_mock

    def test_get_temporal_aggregate_calls_classmethod(self):
        """get_temporal_aggregate must call GetAggregate classmethod on TemporalIndex."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = "42"
        engine.get_temporal_aggregate("svc:auth", "CALLS_AT", "count", 0, 9999)
        mock.classMethodValue.assert_called_once()
        call_args = mock.classMethodValue.call_args[0]
        assert "Graph.KG.TemporalIndex" in call_args
        assert "GetAggregate" in call_args

    def test_get_temporal_aggregate_count_returns_int(self):
        """count metric must return a Python int."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = "100"
        result = engine.get_temporal_aggregate("svc:auth", "CALLS_AT", "count", 0, 9999)
        assert isinstance(result, int)
        assert result == 100

    def test_get_temporal_aggregate_avg_returns_float(self):
        """avg metric must return a Python float."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = "3.141593"
        result = engine.get_temporal_aggregate("svc:auth", "CALLS_AT", "avg", 0, 9999)
        assert isinstance(result, float)
        assert abs(result - 3.141593) < 1e-4

    def test_get_temporal_aggregate_empty_avg_returns_none(self):
        """Empty string result for avg/min/max must return None (empty window)."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = ""
        result = engine.get_temporal_aggregate("svc:auth", "CALLS_AT", "avg", 0, 9999)
        assert result is None

    def test_get_temporal_aggregate_empty_count_returns_zero(self):
        """Empty string result for count must return 0 (not None)."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = ""
        result = engine.get_temporal_aggregate("svc:auth", "CALLS_AT", "count", 0, 9999)
        assert result == 0
        assert isinstance(result, int)

    def test_get_bucket_groups_returns_list(self):
        """get_bucket_groups must parse JSON and return a list of dicts."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = (
            '[{"source":"svc:auth","predicate":"CALLS_AT",'
            '"count":10,"sum":100.0,"avg":10.0,"min":5.0,"max":20.0}]'
        )
        result = engine.get_bucket_groups("CALLS_AT", 0, 9999)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["source"] == "svc:auth"
        assert result[0]["count"] == 10

    # ── ENH-1: sourcePrefix unit tests ──────────────────────────────
    def test_get_bucket_groups_passes_source_prefix(self):
        """source_prefix kwarg is forwarded as 4th positional arg."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '[]'
        engine.get_bucket_groups("COST_ON", 0, 9999, source_prefix="QG:Acme:")
        args = mock.classMethodValue.call_args[0]
        assert args[-1] == "QG:Acme:"

    def test_get_bucket_groups_default_prefix_is_empty(self):
        """Omitting source_prefix passes empty string (backward compat)."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '[]'
        engine.get_bucket_groups("COST_ON", 0, 9999)
        args = mock.classMethodValue.call_args[0]
        assert args[-1] == ""

    # ── ENH-2: GetBucketGroupTargets unit tests ──────────────────────
    def test_get_bucket_group_targets_returns_list(self):
        """get_bucket_group_targets parses JSON array of strings."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '["QG:A:P:g1","QG:A:P:g2"]'
        result = engine.get_bucket_group_targets("Rtn:A:P:proc", "CALLED_BY", 0, 9999)
        assert isinstance(result, list)
        assert set(result) == {"QG:A:P:g1", "QG:A:P:g2"}

    def test_get_bucket_group_targets_empty(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '[]'
        result = engine.get_bucket_group_targets("Rtn:A:P:proc", "CALLED_BY", 0, 9999)
        assert result == []

    # ── ENH-3: docstring test ────────────────────────────────────────
    def test_get_bucket_groups_docstring(self):
        """get_bucket_groups has a docstring documenting all return keys."""
        from iris_vector_graph.engine import IRISGraphEngine
        doc = IRISGraphEngine.get_bucket_groups.__doc__
        assert doc is not None
        for key in ("source", "predicate", "count", "sum", "avg", "min", "max"):
            assert key in doc, f"missing key '{key}' in docstring"

    def test_get_distinct_count_calls_classmethod_returns_int(self):
        """get_distinct_count must call GetDistinctCount and return an int."""
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = "7"
        result = engine.get_distinct_count("svc:auth", "CALLS_AT", 0, 9999)
        mock.classMethodValue.assert_called_once()
        call_args = mock.classMethodValue.call_args[0]
        assert "GetDistinctCount" in call_args
        assert isinstance(result, int)
        assert result == 7


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestTemporalPreAggE2E:

    PREFIX = f"PA_{uuid.uuid4().hex[:6]}"

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        yield
        try:
            self.engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
        except Exception:
            pass

    def test_aggregate_avg_correct(self):
        now = int(time.time())
        weights = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        edges = [
            {"s": f"{self.PREFIX}:src", "p": "CALLS_AT", "o": f"{self.PREFIX}:dst{i}",
             "ts": now + i, "w": w}
            for i, w in enumerate(weights)
        ]
        self.engine.bulk_create_edges_temporal(edges)
        expected_avg = sum(weights) / len(weights)
        result = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:src", "CALLS_AT", "avg", now - 10, now + 100)
        assert result is not None
        assert abs(result - expected_avg) < 0.001

    def test_aggregate_count_correct(self):
        now = int(time.time())
        edges = [
            {"s": f"{self.PREFIX}:csrc", "p": "CALLS_AT",
             "o": f"{self.PREFIX}:cdst{i}", "ts": now + i}
            for i in range(10)
        ]
        self.engine.bulk_create_edges_temporal(edges)
        count = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:csrc", "CALLS_AT", "count", now - 10, now + 100)
        assert count == 10
        assert isinstance(count, int)

    def test_bucket_groups_all_sources(self):
        now = int(time.time())
        for src in ["alpha", "beta", "gamma"]:
            edges = [
                {"s": f"{self.PREFIX}:{src}", "p": "SENDS",
                 "o": f"{self.PREFIX}:dst{i}", "ts": now + i}
                for i in range(5)
            ]
            self.engine.bulk_create_edges_temporal(edges)
        groups = self.engine.get_bucket_groups("SENDS", now - 10, now + 100)
        sources = {g["source"] for g in groups}
        assert f"{self.PREFIX}:alpha" in sources
        assert f"{self.PREFIX}:beta" in sources
        assert f"{self.PREFIX}:gamma" in sources
        for g in groups:
            if g["source"].startswith(self.PREFIX):
                assert g["count"] == 5

    def test_multi_bucket_aggregate(self):
        bucket_sec = 300
        now = int(time.time())
        t1 = now - (now % bucket_sec) - bucket_sec
        t2 = t1 + bucket_sec
        edges = (
            [{"s": f"{self.PREFIX}:mb", "p": "HIT", "o": f"{self.PREFIX}:x{i}",
              "ts": t1 + i, "w": 1.0} for i in range(5)] +
            [{"s": f"{self.PREFIX}:mb", "p": "HIT", "o": f"{self.PREFIX}:y{i}",
              "ts": t2 + i, "w": 3.0} for i in range(5)]
        )
        self.engine.bulk_create_edges_temporal(edges)
        count = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:mb", "HIT", "count", t1, t2 + 100)
        assert count == 10
        total_sum = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:mb", "HIT", "sum", t1, t2 + 100)
        assert abs(total_sum - 20.0) < 0.01

    def test_distinct_count_nonzero(self):
        now = int(time.time())
        edges = [
            {"s": f"{self.PREFIX}:dc", "p": "CALLS_AT",
             "o": f"{self.PREFIX}:target{i}", "ts": now + i}
            for i in range(20)
        ]
        self.engine.bulk_create_edges_temporal(edges)
        estimate = self.engine.get_distinct_count(
            f"{self.PREFIX}:dc", "CALLS_AT", now - 10, now + 100)
        assert estimate > 0
        assert isinstance(estimate, int)

    def test_purge_clears_tagg(self):
        now = int(time.time())
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:pur", "p": "X", "o": f"{self.PREFIX}:y", "ts": now}
        ])
        before = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:pur", "X", "count", now - 10, now + 10)
        assert before > 0
        self.engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
        after = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:pur", "X", "count", now - 10, now + 10)
        assert after == 0


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestTemporalAPIGapsE2E:

    PREFIX = f"GAP_{uuid.uuid4().hex[:6]}"

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        yield
        try:
            self.engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
        except Exception:
            pass

    def test_get_edges_in_window_returns_long_key_aliases(self):
        now = int(time.time())
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:src", "p": "CALLS_AT",
             "o": f"{self.PREFIX}:dst", "ts": now, "w": 42.0}
        ])
        edges = self.engine.get_edges_in_window(
            f"{self.PREFIX}:src", "CALLS_AT", now - 10, now + 10)
        assert len(edges) == 1
        e = edges[0]
        assert e["source"]    == f"{self.PREFIX}:src"
        assert e["target"]    == f"{self.PREFIX}:dst"
        assert e["predicate"] == "CALLS_AT"
        assert e["timestamp"] == now
        assert e["weight"]    == 42.0
        assert e["s"] == e["source"]
        assert e["o"] == e["target"]
        assert e["p"] == e["predicate"]
        assert e["ts"] == e["timestamp"]
        assert e["w"] == e["weight"]

    def test_get_edges_in_window_inbound_direction(self):
        now = int(time.time())
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:a", "p": "CALLS_AT",
             "o": f"{self.PREFIX}:target", "ts": now, "w": 1.0},
            {"s": f"{self.PREFIX}:b", "p": "CALLS_AT",
             "o": f"{self.PREFIX}:target", "ts": now + 1, "w": 2.0},
        ])
        edges = self.engine.get_edges_in_window(
            f"{self.PREFIX}:target", "CALLS_AT",
            now - 10, now + 10, direction="in")
        assert len(edges) == 2
        sources = {e["source"] for e in edges}
        assert f"{self.PREFIX}:a" in sources
        assert f"{self.PREFIX}:b" in sources

    def test_upsert_temporal_edge_no_duplicate(self):
        now = int(time.time())
        for _ in range(3):
            self.engine.create_edge_temporal(
                f"{self.PREFIX}:src", "COST_ON", f"Date:2026-03-18",
                now, 27.7, upsert=True)
        count = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:src", "COST_ON", "count", now - 10, now + 10)
        assert count == 1

    def test_upsert_false_allows_duplicates(self):
        now = int(time.time())
        for _ in range(3):
            self.engine.create_edge_temporal(
                f"{self.PREFIX}:dup", "COST_ON", f"Date:2026-03-19",
                now, 10.0, upsert=False)
        count = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:dup", "COST_ON", "count", now - 10, now + 10)
        assert count == 3

    # ── ENH-1: sourcePrefix filter on GetBucketGroups ──────────────────
    def test_bucket_groups_source_prefix_filters(self):
        """get_bucket_groups(source_prefix=...) returns only matching sources."""
        now = int(time.time())
        for tenant in ["AcmeCorp", "OtherCo"]:
            edges = [
                {"s": f"Routine:{tenant}:PROD:r{i}", "p": "CALLED_BY",
                 "o": f"QueryGroup:{tenant}:PROD:g1", "ts": now + i, "w": 1.0}
                for i in range(3)
            ]
            self.engine.bulk_create_edges_temporal(edges)
        filtered = self.engine.get_bucket_groups(
            "CALLED_BY", now - 10, now + 100, source_prefix="Routine:AcmeCorp:")
        sources = {g["source"] for g in filtered}
        assert all(s.startswith("Routine:AcmeCorp:") for s in sources), sources
        assert not any(s.startswith("Routine:OtherCo:") for s in sources), sources

    def test_bucket_groups_empty_prefix_returns_all(self):
        """Empty source_prefix preserves backward-compat (all sources returned)."""
        now = int(time.time())
        for tenant in ["TenA", "TenB"]:
            self.engine.bulk_create_edges_temporal([
                {"s": f"Rtn:{tenant}:x", "p": "CALLED_BY",
                 "o": f"QG:{tenant}:g", "ts": now, "w": 1.0}
            ])
        all_groups = self.engine.get_bucket_groups("CALLED_BY", now - 10, now + 10)
        sources = {g["source"] for g in all_groups}
        assert any("TenA" in s for s in sources)
        assert any("TenB" in s for s in sources)

    # ── ENH-2: GetBucketGroupTargets ──────────────────────────────────
    def test_bucket_group_targets_returns_correct_targets(self):
        """get_bucket_group_targets returns distinct targets for a source."""
        now = int(time.time())
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:ProcessHL7", "p": "CALLED_BY",
             "o": f"{self.PREFIX}:G1", "ts": now, "w": 1.0},
            {"s": f"{self.PREFIX}:ProcessHL7", "p": "CALLED_BY",
             "o": f"{self.PREFIX}:G2", "ts": now + 1, "w": 2.0},
            {"s": f"{self.PREFIX}:OtherRtn", "p": "CALLED_BY",
             "o": f"{self.PREFIX}:G3", "ts": now + 2, "w": 1.0},
        ])
        targets = self.engine.get_bucket_group_targets(
            f"{self.PREFIX}:ProcessHL7", "CALLED_BY", now - 10, now + 100)
        assert isinstance(targets, list)
        assert set(targets) == {f"{self.PREFIX}:G1", f"{self.PREFIX}:G2"}

    def test_bucket_group_targets_deduplicates_across_buckets(self):
        """Targets appearing in multiple buckets are returned only once."""
        bucket_sec = 300
        now = int(time.time())
        t1 = now - (now % bucket_sec) - bucket_sec
        t2 = t1 + bucket_sec
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:dup", "p": "CALLED_BY",
             "o": f"{self.PREFIX}:same_target", "ts": t1, "w": 1.0},
            {"s": f"{self.PREFIX}:dup", "p": "CALLED_BY",
             "o": f"{self.PREFIX}:same_target", "ts": t2, "w": 2.0},
        ])
        targets = self.engine.get_bucket_group_targets(
            f"{self.PREFIX}:dup", "CALLED_BY", t1 - 10, t2 + 100)
        assert targets.count(f"{self.PREFIX}:same_target") == 1

    def test_bucket_group_targets_empty_when_outside_window(self):
        """No targets returned when edges are outside the time window."""
        now = int(time.time())
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:ow", "p": "CALLED_BY",
             "o": f"{self.PREFIX}:t1", "ts": now + 10000, "w": 1.0},
        ])
        targets = self.engine.get_bucket_group_targets(
            f"{self.PREFIX}:ow", "CALLED_BY", now - 100, now + 100)
        assert targets == []

    def test_purge_before_removes_old_edges(self):
        now = int(time.time())
        old_ts = now - 7200
        self.engine.bulk_create_edges_temporal([
            {"s": f"{self.PREFIX}:pb", "p": "COST_ON",
             "o": "Date:old", "ts": old_ts, "w": 1.0},
            {"s": f"{self.PREFIX}:pb", "p": "COST_ON",
             "o": "Date:new", "ts": now, "w": 2.0},
        ])
        self.engine.purge_before(now - 3600)
        count = self.engine.get_temporal_aggregate(
            f"{self.PREFIX}:pb", "COST_ON", "count", 0, now + 10)
        assert count == 1
        edges = self.engine.get_edges_in_window(
            f"{self.PREFIX}:pb", "COST_ON", 0, now + 10)
        assert edges[0]["target"] == "Date:new"
