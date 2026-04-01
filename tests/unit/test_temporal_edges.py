"""Unit and e2e tests for temporal edge indexing."""
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
            irispy = iris.createIRIS(self.conn)
            val = irispy.get("^KG", "out", f"{self.PREFIX}:BC", "COMPAT", f"{self.PREFIX}:BC2")
            assert val is not None
        except (TypeError, ImportError):
            pytest.skip("iris.createIRIS not available on this Python version")
