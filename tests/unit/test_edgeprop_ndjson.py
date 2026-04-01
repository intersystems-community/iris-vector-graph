"""Unit and e2e tests for edge properties (edgeprop) and NDJSON import/export."""
import json
import os
import tempfile
import time
import uuid
import pytest
from unittest.mock import MagicMock

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class TestEdgePropUnit:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        iris_mock = MagicMock()
        e._iris_obj = lambda: iris_mock
        return e, iris_mock

    def test_create_edge_temporal_with_attrs(self):
        engine, mock = self._make_engine()
        engine.create_edge_temporal("A", "REL", "B", 1000, attrs={"latency_ms": "237"})
        call_args = mock.classMethodVoid.call_args[0]
        assert "latency_ms" in call_args[-1]

    def test_get_edge_attrs_returns_dict(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '{"latency_ms":"237","error":"true"}'
        result = engine.get_edge_attrs(1000, "A", "REL", "B")
        assert result == {"latency_ms": "237", "error": "true"}

    def test_get_edge_attrs_empty(self):
        engine, mock = self._make_engine()
        mock.classMethodValue.return_value = '{}'
        result = engine.get_edge_attrs(1000, "A", "REL", "B")
        assert result == {}

    def test_get_edges_in_window_include_attrs(self):
        pass


class TestNdjsonUnit:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        cursor_mock = MagicMock()
        cursor_mock.fetchone.return_value = (0,)
        e.conn.cursor.return_value = cursor_mock
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "0"
        e._iris_obj = lambda: iris_mock
        return e

    def test_import_ndjson_3_lines(self):
        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"kind":"node","id":"svc:A","labels":["Service"],"properties":{"name":"A"}}\n')
            f.write('{"kind":"node","id":"svc:B","labels":["Service"],"properties":{"name":"B"}}\n')
            f.write('{"kind":"temporal_edge","source":"svc:A","predicate":"CALLS_AT","target":"svc:B","timestamp":1000,"weight":1.0,"attrs":{"latency_ms":"237"}}\n')
            path = f.name
        result = engine.import_graph_ndjson(path)
        assert result["nodes"] == 2
        assert result["temporal_edges"] == 1
        os.unlink(path)

    def test_import_upserts_duplicate_nodes(self):
        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"kind":"node","id":"svc:A","labels":["Service"]}\n')
            f.write('{"kind":"node","id":"svc:A","labels":["Service"]}\n')
            path = f.name
        result = engine.import_graph_ndjson(path)
        assert result["nodes"] == 2
        os.unlink(path)

    def test_import_skips_unknown_kind(self):
        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"kind":"unknown","data":"foo"}\n')
            f.write('{"kind":"node","id":"svc:A","labels":["Service"]}\n')
            path = f.name
        result = engine.import_graph_ndjson(path)
        assert result["nodes"] == 1
        os.unlink(path)

    def test_export_ndjson_placeholder(self):
        pass


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestEdgePropNdjsonE2E:

    PREFIX = f"EP_{uuid.uuid4().hex[:6]}"

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

    def test_attrs_roundtrip(self):
        now = int(time.time())
        self.engine.create_edge_temporal(
            f"{self.PREFIX}:A", "CALLS", f"{self.PREFIX}:B", now,
            attrs={"latency_ms": "237", "error": "true", "trace_id": "abc123"})
        attrs = self.engine.get_edge_attrs(now, f"{self.PREFIX}:A", "CALLS", f"{self.PREFIX}:B")
        assert attrs.get("latency_ms") == "237"
        assert attrs.get("error") == "true"
        assert attrs.get("trace_id") == "abc123"

    def test_ndjson_import(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write(json.dumps({"kind": "node", "id": f"{self.PREFIX}:SVC1", "labels": ["Service"], "properties": {"name": "svc1"}}) + "\n")
            f.write(json.dumps({"kind": "node", "id": f"{self.PREFIX}:SVC2", "labels": ["Service"], "properties": {"name": "svc2"}}) + "\n")
            f.write(json.dumps({"kind": "temporal_edge", "source": f"{self.PREFIX}:SVC1", "predicate": "CALLS_AT", "target": f"{self.PREFIX}:SVC2", "timestamp": int(time.time()), "weight": 1.0, "source_labels": ["Service"], "target_labels": ["Service"], "attrs": {"latency_ms": "100"}}) + "\n")
            path = f.name
        result = self.engine.import_graph_ndjson(path)
        assert result["nodes"] >= 2
        assert result["temporal_edges"] >= 1
        os.unlink(path)

    def test_ndjson_roundtrip(self):
        now = int(time.time())
        self.engine.create_edge_temporal(
            f"{self.PREFIX}:RT1", "LINK", f"{self.PREFIX}:RT2", now,
            attrs={"key1": "val1"})
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            export_path = f.name
        result = self.engine.export_temporal_edges_ndjson(export_path, start=now-10, end=now+10)
        assert result["temporal_edges"] >= 1
        with open(export_path) as f:
            lines = f.readlines()
        assert len(lines) >= 1
        event = json.loads(lines[0])
        assert event["kind"] == "temporal_edge"
        os.unlink(export_path)

    def test_bulk_import_with_attrs(self):
        now = int(time.time())
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            for i in range(100):
                f.write(json.dumps({"kind": "temporal_edge", "source": f"{self.PREFIX}:S{i}", "predicate": "CALLS_AT", "target": f"{self.PREFIX}:T{i}", "timestamp": now + i, "weight": 1.0, "attrs": {"idx": str(i)}}) + "\n")
            path = f.name
        result = self.engine.import_graph_ndjson(path)
        assert result["temporal_edges"] == 100
        attrs = self.engine.get_edge_attrs(now + 50, f"{self.PREFIX}:S50", "CALLS_AT", f"{self.PREFIX}:T50")
        assert attrs.get("idx") == "50"
        os.unlink(path)
