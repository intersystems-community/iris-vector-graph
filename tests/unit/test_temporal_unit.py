"""
Unit tests for _engine/temporal.py covering:
- create_edge_temporal: with graph= param (inserts into nodes + rdf_edges)
- bulk_create_edges_temporal: with graph= param (count > 0 path)
- get_edges_in_window: result.error=True path, column-mapping path
- export_temporal_edges_ndjson: writes NDJSON file
- get_temporal_aggregate: rows present (count metric, float metric)

No live IRIS connection — mocks store and iris_obj.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, mock_open
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# create_edge_temporal
# ---------------------------------------------------------------------------

class TestCreateEdgeTemporal:

    def test_creates_without_graph(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[], error=None)
        eng._store = store
        result = eng.create_edge_temporal("n1", "TREATS", "n2", timestamp=1000)
        assert result is True
        store.write_temporal_edge.assert_called_once()

    def test_creates_with_graph_inserts_nodes_and_edges(self):
        """graph= param triggers additional INSERT INTO nodes + rdf_edges."""
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[], error=None)
        eng._store = store
        result = eng.create_edge_temporal("n1", "TREATS", "n2", timestamp=1000, graph="g1")
        assert result is True
        # cursor should have been called for node inserts + edge insert
        assert cursor.execute.called

    def test_error_result_returns_false(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[], error="fail")
        eng._store = store
        result = eng.create_edge_temporal("n1", "TREATS", "n2", timestamp=1000)
        assert result is False

    def test_no_timestamp_defaults_to_zero(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[], error=None)
        eng._store = store
        result = eng.create_edge_temporal("n1", "TREATS", "n2")
        store.write_temporal_edge.assert_called_once()
        _, kwargs = store.write_temporal_edge.call_args
        assert kwargs.get("timestamp", 0) == 0


# ---------------------------------------------------------------------------
# bulk_create_edges_temporal
# ---------------------------------------------------------------------------

class TestBulkCreateEdgesTemporal:

    def test_returns_count(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.bulk_write_temporal_edges.return_value = IVGResult(columns=["count"], rows=[[3]])
        eng._store = store
        edges = [
            {"s": "n1", "p": "TREATS", "o": "n2", "ts": 1000, "w": 1.0},
            {"source": "n2", "predicate": "TARGETS", "target": "n3", "timestamp": 2000},
        ]
        result = eng.bulk_create_edges_temporal(edges)
        assert result == 3

    def test_with_graph_inserts_nodes_and_edges(self):
        """count > 0 + graph= triggers additional node/edge inserts."""
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.bulk_write_temporal_edges.return_value = IVGResult(columns=["count"], rows=[[2]])
        eng._store = store
        edges = [{"s": "n1", "p": "TREATS", "o": "n2", "ts": 100}]
        result = eng.bulk_create_edges_temporal(edges, graph="g1")
        assert result == 2
        assert cursor.execute.called

    def test_empty_rows_returns_zero(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.bulk_write_temporal_edges.return_value = IVGResult(columns=["count"], rows=[])
        eng._store = store
        result = eng.bulk_create_edges_temporal([])
        assert result == 0


# ---------------------------------------------------------------------------
# get_edges_in_window
# ---------------------------------------------------------------------------

class TestGetEdgesInWindow:

    def test_error_returns_empty(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_temporal_window_query.return_value = IVGResult(
            columns=[], rows=[], error="some error"
        )
        eng._store = store
        result = eng.get_edges_in_window("n1", "TREATS", 0, 999)
        assert result == []

    def test_with_column_names_returns_mapped_dicts(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_temporal_window_query.return_value = IVGResult(
            columns=["source", "predicate", "target", "timestamp", "weight"],
            rows=[["n1", "TREATS", "n2", 1000, 1.0]],
        )
        eng._store = store
        result = eng.get_edges_in_window("n1", "TREATS", 0, 9999)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_raw_rows_returned_when_no_columns(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_temporal_window_query.return_value = IVGResult(
            columns=[],
            rows=[["n1", "TREATS", "n2"]],
        )
        eng._store = store
        result = eng.get_edges_in_window()
        assert result == [["n1", "TREATS", "n2"]]


# ---------------------------------------------------------------------------
# export_temporal_edges_ndjson
# ---------------------------------------------------------------------------

class TestExportTemporalEdgesNdjson:

    def test_writes_ndjson_file(self, tmp_path):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        edges = [{"ts": 1000, "s": "n1", "p": "TREATS", "o": "n2", "w": 0.9}]
        iris_obj.classMethodValue.side_effect = [
            json.dumps(edges),           # QueryWindow
            json.dumps({"confidence": 0.9}),  # GetEdgeAttrs
        ]
        eng._arno_capabilities = {}
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            out_path = str(tmp_path / "out.ndjson")
            result = eng.export_temporal_edges_ndjson(out_path)
        assert result["temporal_edges"] == 1
        with open(out_path) as f:
            line = json.loads(f.readline())
        assert line["source"] == "n1"
        assert line["predicate"] == "TREATS"


# ---------------------------------------------------------------------------
# get_temporal_aggregate
# ---------------------------------------------------------------------------

class TestGetTemporalAggregate:

    def test_count_metric_returns_int(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.get_temporal_aggregate.return_value = IVGResult(columns=["val"], rows=[[42]])
        eng._store = store
        result = eng.get_temporal_aggregate("n1", "TREATS", "count", 0, 9999)
        assert result == 42
        assert isinstance(result, int)

    def test_sum_metric_returns_float(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.get_temporal_aggregate.return_value = IVGResult(columns=["val"], rows=[[3.14]])
        eng._store = store
        result = eng.get_temporal_aggregate("n1", "TREATS", "sum", 0, 9999)
        assert result == pytest.approx(3.14)

    def test_no_rows_count_returns_zero(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.get_temporal_aggregate.return_value = IVGResult(columns=["val"], rows=[])
        eng._store = store
        result = eng.get_temporal_aggregate("n1", "TREATS", "count", 0, 9999)
        assert result == 0

    def test_no_rows_float_returns_none(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.get_temporal_aggregate.return_value = IVGResult(columns=["val"], rows=[])
        eng._store = store
        result = eng.get_temporal_aggregate("n1", "TREATS", "avg", 0, 9999)
        assert result is None
